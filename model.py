import tensorflow as tf

from network import network
from loss import wing_loss
from metrics import nme_metric_ops


def model_fn(features, labels, mode, params, config):
    """
    This is a function for creating a tensorflow computational graph.
    The function is in the format required by tf.estimator.
    """

    is_training = mode == tf.estimator.ModeKeys.TRAIN
    landmarks = network(features, is_training, num_landmarks=params['num_landmarks'])
    # (features are just a tensor of RGB images)

    if mode == tf.estimator.ModeKeys.PREDICT:
        predictions = {'landmarks': landmarks}
        export_outputs = tf.estimator.export.PredictOutput({
            name: tf.identity(tensor, name)
            for name, tensor in predictions.items()
        })
        return tf.estimator.EstimatorSpec(
            mode, predictions=predictions,
            export_outputs={'outputs': export_outputs}
        )

    with tf.name_scope('rescale'):
        w, h = params['image_size']
        scaler = tf.constant([h, w], dtype=tf.float32)
        labels = labels * scaler
        landmarks = landmarks * scaler

    loss = wing_loss(landmarks, labels, w=params['w'], epsilon=params['epsilon'])
    tf.losses.add_loss(loss)
    tf.summary.scalar('just_wing_loss', loss)

    # add L2 regularization
    with tf.name_scope('weight_decay'):
        add_weight_decay(params['weight_decay'])
        regularization_loss = tf.losses.get_regularization_loss()
        tf.summary.scalar('regularization_loss', regularization_loss)

    total_loss = tf.losses.get_total_loss(add_regularization_losses=True)

    if mode == tf.estimator.ModeKeys.EVAL:
        eval_metric_ops = {
            'validation_mae': tf.metrics.mean_absolute_error(labels, landmarks),
            'normalized_mean_error': nme_metric_ops(labels, landmarks)
        }
        return tf.estimator.EstimatorSpec(
            mode, loss=total_loss,
            eval_metric_ops=eval_metric_ops
        )

    assert mode == tf.estimator.ModeKeys.TRAIN

    with tf.name_scope('evaluation_ops'):
        mae = tf.reduce_mean(tf.abs(labels - landmarks), axis=[0, 1, 2])

    with tf.variable_scope('learning_rate'):
        global_step = tf.train.get_global_step()
        learning_rate = tf.train.piecewise_constant(
            global_step, params['lr_boundaries'], params['lr_values']
        )
        tf.summary.scalar('learning_rate', learning_rate)

    update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
    with tf.control_dependencies(update_ops), tf.variable_scope('optimizer'):
        optimizer = tf.train.AdamOptimizer(learning_rate)
        grads_and_vars = optimizer.compute_gradients(total_loss)
        train_op = optimizer.apply_gradients(grads_and_vars, global_step)

    for g, v in grads_and_vars:
        tf.summary.histogram(v.name[:-2] + '_hist', v)
        tf.summary.histogram(v.name[:-2] + '_grad_hist', g)

    tf.summary.scalar('train_mae', mae)
    return tf.estimator.EstimatorSpec(mode, loss=total_loss, train_op=train_op)


def add_weight_decay(weight_decay):
    """Add L2 regularization to all (or some) trainable kernel weights."""
    weight_decay = tf.constant(
        weight_decay, tf.float32,
        [], 'weight_decay'
    )
    trainable_vars = tf.trainable_variables()
    kernels = [v for v in trainable_vars if 'weights' in v.name]
    for K in kernels:
        x = tf.multiply(weight_decay, tf.nn.l2_loss(K))
        tf.add_to_collection(tf.GraphKeys.REGULARIZATION_LOSSES, x)
